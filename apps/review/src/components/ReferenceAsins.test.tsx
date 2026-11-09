import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import ReferenceAsins from "./ReferenceAsins";
afterEach(cleanup);
it("直接外链打开且不清空审核意见，无中间弹窗", () => {
  render(<><input aria-label="审核意见" defaultValue="继续核对" /><ReferenceAsins value="B0TEST0005 https://amazon.com/dp/B0TEST0006" site="CA" /></>);
  const links=screen.getAllByRole('link');
  expect(links[0]).toHaveAttribute('href','https://www.amazon.ca/dp/B0TEST0005');
  expect(links[0]).toHaveAttribute('target','_blank');
  expect(links[0]).toHaveAttribute('rel','noopener noreferrer');
  expect(links[1]).toHaveAttribute('title',expect.stringContaining('跨站参考'));
  fireEvent.click(links[0]);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByRole('textbox')).toHaveValue('继续核对');
});
it("空值与非法文本保留可理解的提示", () => {
  const {rerender}=render(<ReferenceAsins value="" site="CA" />);
  expect(screen.getByText('竞品未填写')).toBeInTheDocument();
  rerender(<ReferenceAsins value="ASIN错误" site="CA" />);
  expect(screen.getByText('格式待核对')).toHaveAttribute('title','ASIN错误');
  expect(screen.queryByRole('link')).not.toBeInTheDocument();
});
